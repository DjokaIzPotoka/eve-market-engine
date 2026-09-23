import type {
  HaulRequest,
  HaulResult,
  MarketMakingRow,
  Settings,
  StatusReport,
  Station,
  Trade,
} from "../types";
import { httpApi } from "./http";
import { mockApi } from "./mock";

/** The single seam between UI and backend. `VITE_API=mock` (see `npm run dev:mock`) uses canned data. */
export interface EveApi {
  getStatus(): Promise<StatusReport>;
  getStations(): Promise<Station[]>;
  runHaul(req: HaulRequest): Promise<HaulResult>;
  getMarketMaking(): Promise<MarketMakingRow[]>;
  getTrades(): Promise<Trade[]>;
  getSettings(): Promise<Settings>;
  saveSettings(s: Settings): Promise<Settings>;
}

export const api: EveApi = import.meta.env.VITE_API === "mock" ? mockApi : httpApi;
