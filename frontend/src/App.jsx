import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { HomePage } from './pages/HomePage.jsx'
import { ApplicationPage } from './pages/ApplicationPage.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/applications/:appSlug" element={<ApplicationPage />} />
      </Routes>
    </BrowserRouter>
  )
}
