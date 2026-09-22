import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api } from "./client";

// api/client.ts is thin (URL construction + JSON parsing over fetch), so
// these tests mock global.fetch and assert on the request it issues,
// rather than re-testing fetch itself. The main risk here is query-string
// construction: from/to are RFC3339 timestamps containing ":" and "+"/"Z",
// which must be encodeURIComponent'd or the query string breaks.
describe("api client URL construction", () => {
  const okResponse = (body: unknown) =>
    new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getRangeChart encodes RFC3339 timestamps in the query string", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(okResponse([]));

    await api.getRangeChart("2026-09-21T07:00:00.000Z", "2026-09-22T07:00:00.000Z");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "http://localhost:9082/chart?from=2026-09-21T07%3A00%3A00.000Z&to=2026-09-22T07%3A00%3A00.000Z"
    );
  });

  it("getBolusRangeChart hits /bolus with encoded from/to", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(okResponse({ doses: [], food_activity: [], correction_activity: [] }));

    await api.getBolusRangeChart("2026-09-21T07:00:00.000Z", "2026-09-22T07:00:00.000Z");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/bolus?from=");
    expect(url).not.toContain(":00:00"); // unescaped colons would leak through verbatim
  });

  it("getBasalRangeChart hits /basal with encoded from/to", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(okResponse({ points: [], window_end: "2026-09-22T07:00:00.000Z" }));

    await api.getBasalRangeChart("2026-09-21T07:00:00.000Z", "2026-09-22T07:00:00.000Z");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/basal?from=");
  });

  it("getLastXh builds a path with the hours param, no query string", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(okResponse([]));

    await api.getLastXh(24);

    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:9082/lastxh/24");
  });

  it("getLastXhOffset builds a path with both hours and offset", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(okResponse([]));

    await api.getLastXhOffset(24, 12);

    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:9082/lastxh/24/offset/12");
  });

  it("throws with status text on a non-OK response instead of parsing JSON", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(new Response("", { status: 500, statusText: "Internal Server Error" }));

    await expect(api.getFirstDate()).rejects.toThrow("500 Internal Server Error");
  });
});
