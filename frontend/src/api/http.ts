import type { EveApi } from "./index";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "API nedostupan (da li radi `eve serve`?)");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail
          .map((d: { loc?: string[]; msg: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`)
          .join("; ");
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const httpApi: EveApi = {
  getStatus: () => request("/api/status"),
  getStations: () => request("/api/stations"),
  runHaul: (req) => request("/api/haul", { method: "POST", body: JSON.stringify(req) }),
  getMarketMaking: () => request("/api/market-making"),
  getTrades: () => request("/api/trades"),
  getSettings: () => request("/api/settings"),
  saveSettings: (s) => request("/api/settings", { method: "PUT", body: JSON.stringify(s) }),
};
