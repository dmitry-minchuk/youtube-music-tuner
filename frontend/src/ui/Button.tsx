import type { ButtonHTMLAttributes } from "react";
import styles from "@/ui/Button.module.css";

type Variant = "primary" | "secondary" | "danger";

export function Button({
  variant = "secondary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }): React.JSX.Element {
  const classes = [styles.button, styles[variant], className].filter(Boolean).join(" ");
  return <button type="button" className={classes} {...props} />;
}
