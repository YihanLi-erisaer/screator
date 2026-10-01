import { Check, ChevronDown } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

export type SelectOption = {
  value: string;
  label: string;
  disabled?: boolean;
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
  const [above, setAbove] = useState(false);
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
    const bounds = root.current?.getBoundingClientRect();
    const container = root.current?.closest("dialog")?.getBoundingClientRect();
    const bottom = Math.min(window.innerHeight, container?.bottom ?? window.innerHeight);
    const top = Math.max(0, container?.top ?? 0);
    const menuHeight = Math.min(240, options.length * 38 + 12);
    setAbove(!!bounds && bottom - bounds.bottom < menuHeight && bounds.top - top > bottom - bounds.bottom);
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
            const match = options.findIndex((option) => !option.disabled && option.label.toLocaleLowerCase().startsWith(search.current));
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
        <div ref={menu} id={listId} className={`styled-select-menu ${above ? "above" : ""}`} role="listbox" aria-label={label}>
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
