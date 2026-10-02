import { Check, ChevronDown } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { uiText } from "./i18n";

export type SelectOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

type MenuPlacement = {
  top?: number;
  bottom?: number;
  left: number;
  width?: number;
  minWidth: number;
  maxHeight: number;
};

export function StyledSelect({
  value,
  options,
  onChange,
  label,
  disabled = false,
  className = "",
}: {
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  label: string;
  disabled?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<MenuPlacement | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<(HTMLDivElement | null)[]>([]);
  const search = useRef("");
  const searchTimer = useRef<number | undefined>(undefined);
  const listId = useId();
  const selectedIndex = options.findIndex((option) => option.value === value);
  const selected = options[selectedIndex];

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      const button = trigger.current;
      const list = menu.current;
      if (!button || !list) return;
      const bounds = button.getBoundingClientRect();
      const dialog = root.current?.closest("dialog")?.getBoundingClientRect();
      const topLimit = Math.max(8, (dialog?.top ?? 0) + 8);
      const bottomLimit = Math.min(window.innerHeight - 8, (dialog?.bottom ?? window.innerHeight) - 8);
      if (bounds.bottom < topLimit || bounds.top > bottomLimit) {
        setOpen(false);
        return;
      }
      const below = Math.max(0, bottomLimit - bounds.bottom - 5);
      const aboveSpace = Math.max(0, bounds.top - topLimit - 5);
      const above = below < Math.min(240, list.scrollHeight) && aboveSpace > below;
      const compact = !!root.current?.parentElement?.matches(".button-row, .setting-row");
      const width = compact
        ? Math.max(bounds.width, list.getBoundingClientRect().width)
        : Math.min(bounds.width, window.innerWidth - 16);
      const left = Math.max(8, Math.min(bounds.left, window.innerWidth - width - 8));
      setPlacement({
        top: above ? undefined : bounds.bottom + 5,
        bottom: above ? window.innerHeight - bounds.top + 5 : undefined,
        left,
        width: compact ? undefined : width,
        minWidth: Math.min(bounds.width, window.innerWidth - 16),
        maxHeight: Math.min(240, above ? aboveSpace : below),
      });
    };
    position();
    const onScroll = (event: Event) => {
      if (event.target !== menu.current) position();
    };
    document.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", position);
    return () => {
      document.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", position);
    };
  }, [open, options.length]);

  useEffect(() => {
    const list = menu.current;
    const option = optionRefs.current[activeIndex];
    if (!open || !list || !option) return;
    if (option.offsetTop < list.scrollTop) list.scrollTop = option.offsetTop;
    else if (option.offsetTop + option.offsetHeight > list.scrollTop + list.clientHeight) {
      list.scrollTop = option.offsetTop + option.offsetHeight - list.clientHeight;
    }
  }, [activeIndex, open]);

  useEffect(() => () => window.clearTimeout(searchTimer.current), []);

  const show = (initialIndex = selectedIndex >= 0 ? selectedIndex : options.findIndex((option) => !option.disabled)) => {
    setPlacement(null);
    setActiveIndex(initialIndex);
    setOpen(true);
  };
  const choose = (option: SelectOption) => {
    if (option.disabled) return;
    onChange(option.value);
    setOpen(false);
    trigger.current?.focus();
  };
  const move = (direction: number) => {
    if (!options.some((option) => !option.disabled)) return;
    let next = open ? activeIndex : selectedIndex;
    do {
      next = (next + direction + options.length) % options.length;
    } while (options[next].disabled);
    if (open) setActiveIndex(next);
    else show(next);
  };

  return (
    <div ref={root} className={`styled-select ${className}`}>
      <button
        ref={trigger}
        type="button"
        className="styled-select-trigger"
        role="combobox"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            move(event.key === "ArrowDown" ? 1 : -1);
          } else if (event.key === "Home" || event.key === "End") {
            event.preventDefault();
            const indices = options.map((option, index) => !option.disabled ? index : -1).filter((index) => index >= 0);
            if (indices.length) {
              const target = event.key === "Home" ? indices[0] : indices[indices.length - 1];
              if (open) setActiveIndex(target);
              else show(target);
            }
          } else if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (open && options[activeIndex] && !options[activeIndex].disabled) choose(options[activeIndex]);
            else if (!open) show();
          } else if (event.key === "Escape" && open) {
            event.preventDefault();
            setOpen(false);
          } else if (event.key === "Tab") {
            setOpen(false);
          } else if (event.key.length === 1 && !event.altKey && !event.ctrlKey && !event.metaKey) {
            search.current += event.key.toLocaleLowerCase();
            window.clearTimeout(searchTimer.current);
            searchTimer.current = window.setTimeout(() => { search.current = ""; }, 600);
            const match = options.findIndex((option) => !option.disabled && uiText(option.label).toLocaleLowerCase().startsWith(search.current));
            if (match >= 0) {
              event.preventDefault();
              if (open) setActiveIndex(match);
              else show(match);
            }
          }
        }}
      >
        <span className="styled-select-value">{selected?.label || options[0]?.label || ""}</span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
      {open && (
        <div ref={menu} id={listId} className="styled-select-menu"
          role="listbox" aria-label={label}
          style={{
            top: placement?.top,
            bottom: placement?.bottom,
            left: placement?.left,
            width: placement?.width,
            minWidth: placement?.minWidth,
            maxHeight: placement?.maxHeight,
            visibility: placement ? "visible" : "hidden",
          } satisfies CSSProperties}>
          {options.map((option, index) => (
            <div
              key={option.value}
              id={`${listId}-${index}`}
              ref={(element) => { optionRefs.current[index] = element; }}
              className={`styled-select-option ${index === activeIndex ? "active" : ""}`}
              role="option"
              aria-selected={option.value === value}
              aria-disabled={option.disabled || undefined}
              onMouseEnter={() => { if (!option.disabled) setActiveIndex(index); }}
              onClick={() => choose(option)}
            >
              <span>{option.label}</span>
              {option.value === value && <Check size={15} aria-hidden="true" />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
