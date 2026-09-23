import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { StatusProvider } from "./lib/status";
import { HaulingScreen } from "./screens/Hauling";
import { MarketMakingScreen } from "./screens/MarketMaking";
import { SettingsScreen } from "./screens/SettingsScreen";
import { StatusScreen } from "./screens/Status";
import { TradesScreen } from "./screens/Trades";

export default function App() {
  return (
    <StatusProvider>
      <BrowserRouter basename="/app">
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<StatusScreen />} />
            <Route path="hauling" element={<HaulingScreen />} />
            <Route path="market-making" element={<MarketMakingScreen />} />
            <Route path="trades" element={<TradesScreen />} />
            <Route path="settings" element={<SettingsScreen />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </StatusProvider>
  );
}
