import { act, screen } from "@testing-library/react";
import { ConnectionIndicator } from "@/components/ConnectionIndicator";
import { backoffMs, STREAM_URL } from "@/hooks/useLiveUpdates";
import { FakeEventSource } from "./support/fake-event-source";
import { renderWithClient } from "./support/render";

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function indicator() {
  return screen.getByRole("status");
}

test("backoff doubles up to 30 seconds", () => {
  expect([0, 1, 2, 3, 4, 5, 6, 10].map(backoffMs)).toEqual([
    1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000, 30_000,
  ]);
});

test("the indicator follows the connection", () => {
  renderWithClient(<ConnectionIndicator />);
  expect(FakeEventSource.latest().url).toBe(STREAM_URL);
  expect(indicator()).toHaveTextContent("Connecting to live updates");
  act(() => FakeEventSource.latest().open());
  expect(indicator()).toHaveTextContent("Live");
  expect(indicator()).toHaveAttribute("data-state", "open");
  act(() => FakeEventSource.latest().fail());
  expect(indicator()).toHaveTextContent("Live updates lost, reconnecting");
});

test("reconnects with backoff and resets it once connected", () => {
  renderWithClient(<ConnectionIndicator />);
  const first = FakeEventSource.latest();
  act(() => first.fail());
  expect(first.closed).toBe(true);
  expect(FakeEventSource.instances).toHaveLength(1);
  act(() => vi.advanceTimersByTime(999));
  expect(FakeEventSource.instances).toHaveLength(1);
  act(() => vi.advanceTimersByTime(1));
  expect(FakeEventSource.instances).toHaveLength(2);

  act(() => FakeEventSource.latest().fail());
  act(() => vi.advanceTimersByTime(1_999));
  expect(FakeEventSource.instances).toHaveLength(2);
  act(() => vi.advanceTimersByTime(1));
  expect(FakeEventSource.instances).toHaveLength(3);

  act(() => FakeEventSource.latest().open());
  act(() => FakeEventSource.latest().fail());
  act(() => vi.advanceTimersByTime(1_000));
  expect(FakeEventSource.instances).toHaveLength(4);
});

test("events refetch the data they make stale", () => {
  const { client } = renderWithClient(<ConnectionIndicator />);
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const source = FakeEventSource.latest();
  act(() => source.open());

  act(() => source.emit("vitals", { type: "vitals", mrn: "MRN-001" }));
  expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
    ["census"],
    ["vitals", "MRN-001"],
  ]);

  invalidate.mockClear();
  act(() => source.emit("score", { type: "score", mrn: "MRN-002" }));
  expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
    ["census"],
    ["vitals", "MRN-002"],
  ]);

  invalidate.mockClear();
  act(() => source.emit("alert", { type: "alert", alert_id: "a-1", status: "acknowledged" }));
  expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
    ["census"],
    ["vitals"],
    ["alerts"],
  ]);

  invalidate.mockClear();
  act(() => source.emit("vitals", "not json"));
  expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
    ["census"],
    ["vitals"],
  ]);
});

test("an alert event leaves the list alone while an action is in flight", () => {
  const { client } = renderWithClient(<ConnectionIndicator />);
  vi.spyOn(client, "isMutating").mockReturnValue(1);
  const invalidate = vi.spyOn(client, "invalidateQueries");
  act(() => FakeEventSource.latest().emit("alert", { type: "alert", mrn: "MRN-001" }));
  expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
    ["census"],
    ["vitals", "MRN-001"],
  ]);
});

test("unmounting closes the stream and cancels a pending reconnect", () => {
  const { unmount } = renderWithClient(<ConnectionIndicator />);
  act(() => FakeEventSource.latest().fail());
  unmount();
  act(() => vi.advanceTimersByTime(60_000));
  expect(FakeEventSource.instances).toHaveLength(1);

  FakeEventSource.reset();
  const second = renderWithClient(<ConnectionIndicator />);
  const source = FakeEventSource.latest();
  second.unmount();
  expect(source.closed).toBe(true);
  source.fail();
  act(() => vi.advanceTimersByTime(60_000));
  expect(FakeEventSource.instances).toHaveLength(1);
});
