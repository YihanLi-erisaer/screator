/** Poll visible UI only, with one request at a time and no writes after cleanup. */
export function startVisiblePolling(
  update: (isCurrent: () => boolean) => Promise<void>,
  interval: number,
) {
  let disposed = false;
  let inFlight = false;
  let refreshPending = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const isCurrent = () => !disposed;
  const poll = async () => {
    if (disposed || document.hidden || inFlight) return;
    inFlight = true;
    try {
      await update(isCurrent);
    } catch {
      // A failed read is retried next time. Callers can report errors in update.
    } finally {
      inFlight = false;
      if (!disposed && !document.hidden) {
        if (refreshPending) {
          refreshPending = false;
          void poll();
        } else {
          timer = setTimeout(poll, interval);
        }
      }
    }
  };
  const visibility = () => {
    clearTimeout(timer);
    refreshPending = !document.hidden && inFlight;
    if (!document.hidden) void poll();
  };
  document.addEventListener("visibilitychange", visibility);
  void poll();
  return () => {
    disposed = true;
    clearTimeout(timer);
    document.removeEventListener("visibilitychange", visibility);
  };
}
