import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Chat from "./Chat";

export default function AmigaAI() {
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState("");
  const [mounted, setMounted] = useState(false);
  const dialog = useRef<HTMLDivElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const request = location.pathname.match(/^\/requests\/([^/]+)$/)?.[1] || "";
    setScope(
      location.pathname === "/chat"
        ? new URLSearchParams(location.search).get("request_id") || ""
        : request,
    );
    if (location.pathname === "/chat") {
      setMounted(true);
      setOpen(true);
    }
  }, [location.pathname, location.search]);
  const close = useCallback(() => {
    setOpen(false);
    if (location.pathname === "/chat") navigate("/", { replace: true });
  }, [location.pathname, navigate]);
  useEffect(() => {
    if (!open) return;
    previousFocus.current = document.activeElement as HTMLElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Hide the workspace from assistive technology while the dialog is active.
    const siblings = Array.from(
      dialog.current?.parentElement?.parentElement?.children || [],
    ).filter((element) => element !== dialog.current?.parentElement);
    const original = siblings.map((element) => element.getAttribute("inert"));
    siblings.forEach((element) => element.setAttribute("inert", ""));
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        dialog.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
        ) || [],
      );
      const first = focusable[0],
        last = focusable[focusable.length - 1];
      const outside = !dialog.current?.contains(document.activeElement);
      if (event.shiftKey && (document.activeElement === first || outside)) {
        event.preventDefault();
        last?.focus();
      }
      if (!event.shiftKey && (document.activeElement === last || outside)) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", handleKey, true);
    return () => {
      document.removeEventListener("keydown", handleKey, true);
      document.body.style.overflow = overflow;
      siblings.forEach((element, i) =>
        original[i] === null
          ? element.removeAttribute("inert")
          : element.setAttribute("inert", original[i]!),
      );
      (previousFocus.current?.isConnected &&
      previousFocus.current !== document.body
        ? previousFocus.current
        : launcher.current
      )?.focus();
    };
  }, [open, close]);
  return (
    <>
      <button
        ref={launcher}
        className="amiga-ai-launcher"
        aria-label="Open Amiga AI"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setMounted(true);
          setOpen(true);
        }}
      >
        <span aria-hidden="true">✦</span> Amiga AI
      </button>
      <div
        className="amiga-ai-overlay"
        hidden={!open}
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) close();
        }}
      >
        <div
          ref={dialog}
          className="amiga-ai-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="amiga-ai-title"
        >
          <header className="amiga-ai-header">
            <div className="amiga-ai-symbol" aria-hidden="true">
              ✦
            </div>
            <div>
              <span className="eyebrow">YOUR WORKSPACE INTELLIGENCE</span>
              <h2 id="amiga-ai-title">Amiga AI</h2>
              <p>Clarity for every invoice. Context for every next step.</p>
            </div>
            <button
              className="amiga-ai-close"
              aria-label="Close Amiga AI"
              onClick={close}
            >
              ×
            </button>
          </header>
          {mounted && <Chat scope={scope} />}
          <p className="amiga-ai-footnote">
            Grounded in your records · Governed by your permissions
          </p>
        </div>
      </div>
    </>
  );
}
