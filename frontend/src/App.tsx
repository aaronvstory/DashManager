import { BrowserRouter, Route, Routes } from "react-router-dom"
import { AppLayout } from "@/components/app-layout"
import { Toaster } from "@/components/ui/sonner"
import BatchOtpPage from "@/pages/BatchOtpPage"
import CustomersPage from "@/pages/CustomersPage"
import DaisyPage from "@/pages/DaisyPage"
import DatabasePage from "@/pages/DatabasePage"
import HistoryPage from "@/pages/HistoryPage"
import LiveOtpPage from "@/pages/LiveOtpPage"
import ProxiesPage from "@/pages/ProxiesPage"
import ReportsPage from "@/pages/ReportsPage"
import RunPage from "@/pages/RunPage"
import SettingsPage from "@/pages/SettingsPage"

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<CustomersPage />} />
          <Route path="/daisy" element={<DaisyPage />} />
          <Route path="/database" element={<DatabasePage />} />
          <Route path="/run" element={<RunPage />} />
          <Route path="/otp" element={<LiveOtpPage />} />
          <Route path="/batch-otp" element={<BatchOtpPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/proxies" element={<ProxiesPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Routes>
      <Toaster position="bottom-right" richColors />
    </BrowserRouter>
  )
}
