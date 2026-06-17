import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { AppLayout } from "@/components/app-layout"
import { Toaster } from "@/components/ui/sonner"
import CreatePage from "@/pages/CreatePage"
import CustomersPage from "@/pages/CustomersPage"
import KeepOpenPage from "@/pages/KeepOpenPage"
import DaisyPage from "@/pages/DaisyPage"
import DatabasePage from "@/pages/DatabasePage"
import HistoryPage from "@/pages/HistoryPage"
import OtpPage from "@/pages/OtpPage"
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
          <Route path="/create" element={<CreatePage />} />
          <Route path="/daisy" element={<DaisyPage />} />
          <Route path="/database" element={<DatabasePage />} />
          <Route path="/run" element={<RunPage />} />
          <Route path="/otp" element={<OtpPage />} />
          <Route path="/keep-open" element={<KeepOpenPage />} />
          {/* Live OTP + Batch OTP merged into one page; keep the old batch
              path alive AND in batch mode so bookmarks still land right. */}
          <Route
            path="/batch-otp"
            element={<Navigate to="/otp?mode=batch" replace />}
          />
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
