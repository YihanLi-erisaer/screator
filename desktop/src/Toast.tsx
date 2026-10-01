import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { CheckCircle2, CircleAlert, X } from "lucide-react";

type Toast = { id: number; kind: "error" | "success"; message: string; exiting?: boolean; action?: { label: string; onClick: () => void } };
const EXIT_DURATION_MS = 280;
type ToastContextValue = {
  showError: (message: string, action?: Toast["action"]) => void;
  showSuccess: (message: string) => void;
  dismissToast: (id: number) => void;
  toasts: Toast[];
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("ToastProvider is missing");
  return context;
}

export function ToastViewport() {
  const { toasts, dismissToast } = useToast();
  return (
    <div className="toast-viewport">
      {toasts.map((toast) => (
        <div className={`toast ${toast.kind}-toast${toast.exiting ? " exiting" : ""}`} role={toast.kind === "error" ? "alert" : "status"} key={toast.id}>
          {toast.kind === "error" ? <CircleAlert size={18} aria-hidden="true" /> : <CheckCircle2 size={18} aria-hidden="true" />}
          <span>{toast.message}</span>
          {toast.action && (
            <button className="toast-action" onClick={toast.action.onClick}>
              {toast.action.label}
            </button>
          )}
          <button className="icon-button" aria-label={toast.kind === "error" ? "关闭错误提示" : "关闭成功提示"} onClick={() => dismissToast(toast.id)}>
            <X size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const visible = useRef<Toast[]>([]);
  const nextId = useRef(0);
  const timers = useRef(new Map<number, number>());
  const exitTimers = useRef(new Map<number, number>());
  const dismissToast = useCallback((id: number) => {
    const current = visible.current.find((toast) => toast.id === id);
    if (!current || current.exiting) return;
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    visible.current = visible.current.map((toast) => toast.id === id ? { ...toast, exiting: true } : toast);
    setToasts(visible.current);
    const duration = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : EXIT_DURATION_MS;
    exitTimers.current.set(id, window.setTimeout(() => {
      if (!visible.current.some((toast) => toast.id === id && toast.exiting)) return;
      exitTimers.current.delete(id);
      visible.current = visible.current.filter((toast) => toast.id !== id);
      setToasts(visible.current);
    }, duration));
  }, []);
  const showToast = useCallback((kind: Toast["kind"], message: string, action?: Toast["action"]) => {
    if (!message.trim()) return;
    const id = visible.current.find((toast) => toast.kind === kind && toast.message === message)?.id ?? ++nextId.current;
    window.clearTimeout(timers.current.get(id));
    window.clearTimeout(exitTimers.current.get(id));
    exitTimers.current.delete(id);
    const next = [...visible.current.filter((toast) => toast.id !== id), { id, kind, message, action }];
    visible.current = next;
    setToasts(visible.current);
    timers.current.set(id, window.setTimeout(() => dismissToast(id), 5000));
    const active = next.filter((toast) => !toast.exiting);
    if (active.length > 3) dismissToast(active[0].id);
  }, [dismissToast]);
  const showError = useCallback((message: string, action?: Toast["action"]) => showToast("error", message, action), [showToast]);
  const showSuccess = useCallback((message: string) => showToast("success", message), [showToast]);
  useEffect(() => () => {
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current.clear();
    exitTimers.current.forEach((timer) => window.clearTimeout(timer));
    exitTimers.current.clear();
  }, []);
  return (
    <ToastContext.Provider value={{ showError, showSuccess, dismissToast, toasts }}>
      {children}
      <ToastViewport />
    </ToastContext.Provider>
  );
}

export function ReportError({ message }: { message?: string | null }) {
  const { showError } = useToast();
  useEffect(() => {
    if (message) showError(message);
  }, [message, showError]);
  return null;
}
