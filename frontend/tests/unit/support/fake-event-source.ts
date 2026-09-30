type Listener = (event: MessageEvent<string>) => void;

/** Stands in for the browser's EventSource; tests drive it through `latest()`. */
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, Listener[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  static latest(): FakeEventSource {
    const source = FakeEventSource.instances.at(-1);
    if (!source) throw new Error("no EventSource was opened");
    return source;
  }

  static reset(): void {
    FakeEventSource.instances = [];
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
  }

  open(): void {
    this.onopen?.();
  }

  fail(): void {
    this.onerror?.();
  }

  emit(type: string, data: unknown): void {
    const event = new MessageEvent(type, {
      data: typeof data === "string" ? data : JSON.stringify(data),
    });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}
