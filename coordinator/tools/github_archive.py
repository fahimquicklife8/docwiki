"""Download and safely extract a public GitHub repository."""

from __future__ import annotations

# Used to copy extracted files without loading entire files into memory.
import shutil

# Used to open GitHub's compressed .tar.gz archive.
import tarfile

# Used for clean filesystem path handling.
from pathlib import Path

# Used to parse URLs instead of relying on one complicated regex.
from urllib.parse import quote, urlparse

# Async HTTP client used to communicate with GitHub.
import httpx


# GitHub's REST API base URL.
GITHUB_API = "https://api.github.com"

# Identifies our application to GitHub.
HEADERS = {
    "User-Agent": "DocWiki/0.1",
    "Accept": "application/vnd.github+json",
}

# Maximum compressed archive size: 150 MB.
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024

# Maximum extracted repository size: 400 MB.
MAX_EXTRACTED_BYTES = 400 * 1024 * 1024

# Maximum number of files allowed in one repository.
MAX_FILES = 60_000

# Maximum time allowed for an HTTP operation.
TIMEOUT_SECONDS = 180.0


def validate_github_url(url: str) -> tuple[str, str]:
    """Return the owner and repository from a public GitHub URL."""

    # Remove surrounding whitespace.
    url = url.strip()

    # Convert the URL string into structured URL components.
    parsed = urlparse(url)

    # Only accept HTTPS GitHub URLs without usernames or passwords.
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("UNSUPPORTED_GIT_HOST")

    # Reject query parameters and fragments.
    if parsed.query or parsed.fragment:
        raise ValueError("INVALID_REPOSITORY_URL")

    # Split "/owner/repository" into ["owner", "repository"].
    parts = parsed.path.strip("/").split("/")

    # A repository URL must contain exactly an owner and repository.
    if len(parts) != 2:
        raise ValueError("INVALID_REPOSITORY_URL")

    # Read the owner and repository names.
    owner, repo = parts

    # Convert "repository.git" into "repository".
    if repo.endswith(".git"):
        repo = repo[:-4]

    # Reject empty names and obvious path-traversal characters.
    if (
        not owner
        or not repo
        or ".." in owner
        or ".." in repo
        or "\\" in owner
        or "\\" in repo
    ):
        raise ValueError("INVALID_REPOSITORY_URL")

    # Return the validated GitHub owner and repository.
    return owner, repo


async def resolve_branch_and_commit(
    owner: str,
    repo: str,
    branch: str | None,
) -> tuple[str, str]:
    """Resolve a branch, tag, or SHA to an exact commit SHA."""

    try:
        # Create an asynchronous HTTP client.
        async with httpx.AsyncClient(
            headers=HEADERS,
            timeout=TIMEOUT_SECONDS,
        ) as client:

            # Request basic information about the repository.
            repo_response = await client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}"
            )

            # GitHub returns 404 when the repository is unavailable.
            if repo_response.status_code == 404:
                raise ValueError("REPOSITORY_NOT_FOUND")

            # Treat other unsuccessful responses as download failures.
            if repo_response.status_code != 200:
                raise ValueError("ARCHIVE_DOWNLOAD_FAILED")

            # Convert GitHub's JSON response into a Python dictionary.
            repo_data = repo_response.json()

            # Use GitHub's default branch when no branch was supplied.
            resolved_branch = branch or repo_data["default_branch"]

            # Encode branch names safely because they may contain slashes.
            encoded_branch = quote(resolved_branch, safe="")

            # Ask GitHub for the commit at this branch, tag, or SHA.
            commit_response = await client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/commits/{encoded_branch}"
            )

            # A missing commit normally means the branch does not exist.
            if commit_response.status_code in {404, 422}:
                raise ValueError("BRANCH_NOT_FOUND")

            # Reject any other unsuccessful response.
            if commit_response.status_code != 200:
                raise ValueError("ARCHIVE_DOWNLOAD_FAILED")

            # Extract the exact commit SHA from the JSON response.
            commit_sha = commit_response.json()["sha"]

            # Return the selected branch and exact commit SHA.
            return resolved_branch, commit_sha

    # Preserve our deliberate stable errors.
    except ValueError:
        raise

    # Convert network errors into a stable application error.
    except httpx.HTTPError as exc:
        raise ValueError("ARCHIVE_DOWNLOAD_FAILED") from exc

    # Convert malformed GitHub responses into the same stable error.
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("ARCHIVE_DOWNLOAD_FAILED") from exc


async def download_archive(
    owner: str,
    repo: str,
    commit_sha: str,
    dest_path: Path,
) -> None:
    """Download a commit as a compressed GitHub tarball."""

    # Build GitHub's tarball API URL.
    archive_url = (
        f"{GITHUB_API}/repos/{owner}/{repo}/tarball/{commit_sha}"
    )

    # Ensure the archive's parent directory exists.
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Create a client that follows GitHub's redirect to its archive CDN.
        async with httpx.AsyncClient(
            headers=HEADERS,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            max_redirects=6,
        ) as client:

            # Open a streaming HTTP request.
            async with client.stream("GET", archive_url) as response:

                # Reject unsuccessful responses.
                if response.status_code != 200:
                    raise ValueError("ARCHIVE_DOWNLOAD_FAILED")

                # Track how many compressed bytes have been downloaded.
                downloaded_bytes = 0

                # Open the destination archive file for binary writing.
                with dest_path.open("wb") as archive_file:

                    # Receive the response in 64 KB chunks.
                    async for chunk in response.aiter_bytes(64 * 1024):

                        # Add this chunk's size to the running total.
                        downloaded_bytes += len(chunk)

                        # Stop if the compressed archive exceeds 150 MB.
                        if downloaded_bytes > MAX_DOWNLOAD_BYTES:
                            raise ValueError("REPOSITORY_TOO_LARGE")

                        # Write the chunk to disk.
                        archive_file.write(chunk)

    # Preserve our deliberate stable errors.
    except ValueError:
        raise

    # Convert network failures into a stable application error.
    except httpx.HTTPError as exc:
        raise ValueError("ARCHIVE_DOWNLOAD_FAILED") from exc


def safe_extract(archive_path: Path, dest_dir: Path) -> Path:
    """Safely extract a GitHub tarball and return its root directory."""

    # Create the destination directory if it does not exist.
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Convert it to an absolute normalized path.
    dest_dir = dest_dir.resolve()

    # Track the archive's top-level folder.
    top_directories: set[str] = set()

    # Track the total uncompressed size.
    extracted_bytes = 0

    # Track the number of extracted files.
    extracted_files = 0

    try:
        # Open the compressed tar archive.
        with tarfile.open(archive_path, mode="r:gz") as archive:

            # Examine every archive entry before extracting it.
            for member in archive.getmembers():

                # Convert Windows-style separators to Unix-style separators.
                member_name = member.name.replace("\\", "/")

                # Reject absolute archive paths.
                if member_name.startswith("/"):
                    raise ValueError("UNSAFE_ARCHIVE")

                # Split the member path into individual components.
                path_parts = [
                    part
                    for part in member_name.split("/")
                    if part not in {"", "."}
                ]

                # Ignore empty archive entries.
                if not path_parts:
                    continue

                # Reject paths such as "../../etc/passwd".
                if ".." in path_parts:
                    raise ValueError("UNSAFE_ARCHIVE")

                # Remember this entry's top-level folder.
                top_directories.add(path_parts[0])

                # Build the entry's intended destination.
                output_path = dest_dir.joinpath(*path_parts).resolve()

                # Ensure the final path remains inside the destination.
                if not output_path.is_relative_to(dest_dir):
                    raise ValueError("UNSAFE_ARCHIVE")

                # Ignore symbolic links, hard links, and special device files.
                if member.issym() or member.islnk() or member.isdev():
                    continue

                # Create directories directly.
                if member.isdir():
                    output_path.mkdir(parents=True, exist_ok=True)
                    continue

                # Ignore unsupported archive entry types.
                if not member.isfile():
                    continue

                # Count this regular file.
                extracted_files += 1

                # Add its declared size to the extraction total.
                extracted_bytes += member.size

                # Reject repositories containing too many files.
                if extracted_files > MAX_FILES:
                    raise ValueError("REPOSITORY_TOO_LARGE")

                # Reject repositories that expand beyond 400 MB.
                if extracted_bytes > MAX_EXTRACTED_BYTES:
                    raise ValueError("REPOSITORY_TOO_LARGE")

                # Create the file's parent directories.
                output_path.parent.mkdir(parents=True, exist_ok=True)

                # Obtain a readable stream for this archived file.
                source_file = archive.extractfile(member)

                # Skip the entry if tarfile cannot provide its contents.
                if source_file is None:
                    continue

                # Open the destination file.
                with output_path.open("wb") as destination_file:

                    # Copy incrementally instead of reading the whole file at once.
                    shutil.copyfileobj(
                        source_file,
                        destination_file,
                        length=64 * 1024,
                    )

    # Preserve deliberate stable errors.
    except ValueError:
        raise

    # Convert invalid or corrupted archives into a stable error.
    except (tarfile.TarError, OSError) as exc:
        raise ValueError("UNSAFE_ARCHIVE") from exc

    # GitHub archives should contain exactly one top-level folder.
    if len(top_directories) != 1:
        raise ValueError("UNSAFE_ARCHIVE")

    # Retrieve that single folder name.
    root_name = next(iter(top_directories))

    # Return the extracted repository's root directory.
    return dest_dir / root_name