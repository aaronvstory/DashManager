import { BrowserRouter, Route, Routes } from "react-router-dom"
import { AppLayout } from "@/components/app-layout"
import { Toaster } from "@/components/ui/sonner"
import CustomersPage from "@/pages/CustomersPage"
import HistoryPage from "@/pages/HistoryPage"
import RunPage from "@/pages/RunPage"
import SettingsPage from "@/pages/SettingsPage"

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<CustomersPage />} />
          <Route path="/run" element={<RunPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Routes>
      <Toaster position="bottom-right" richColors />
    </BrowserRouter>
  )
}
