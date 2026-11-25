import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

export function Logo({ size = "text-2xl" }: { size?: string }) {
  return (
    <span className={`${size} font-black tracking-tight`}>
      <span className="bg-gradient-to-r from-fuchsia-400 via-pink-400 to-orange-300 bg-clip-text text-transparent">
        Clipeo
      </span>
    </span>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`glass rounded-3xl p-5 sm:p-6 ${className}`}>{children}</div>;
}

export function Button({
  children,
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" }) {
  const styles = {
    primary: "accent-gradient text-white shadow-lg shadow-fuchsia-500/25",
    ghost: "bg-white/8 border border-white/10 text-white hover:bg-white/12",
    danger: "bg-red-500/15 border border-red-400/30 text-red-200 hover:bg-red-500/25",
  }[variant];
  return (
    <button
      className={`min-h-12 rounded-2xl px-5 font-semibold transition active:scale-[0.98] disabled:opacity-50 disabled:active:scale-100 ${styles} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function Field({ label, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm text-white/70">{label}</span>
      <input className="input-glass" {...props} />
    </label>
  );
}

export function ErrorText({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <p role="alert" className="rounded-xl bg-red-500/10 px-3 py-2 text-sm text-red-200">
      {children}
    </p>
  );
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-white/10">
      <div className="accent-gradient h-full rounded-full transition-all duration-500" style={{ width: `${value}%` }} />
    </div>
  );
}
