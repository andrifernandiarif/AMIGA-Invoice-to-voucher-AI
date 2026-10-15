import { InputHTMLAttributes, useState } from "react";
import { EyeIcon } from "./EyeIcon";

type PasswordInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "onChange" | "value"> & {
  value: string;
  onChange: (value: string) => void;
  ariaLabel?: string;
};

export function PasswordInput({
  onChange,
  ariaLabel,
  ...props
}: PasswordInputProps) {
  const [visible, setVisible] = useState(false);
  return (
    <span className="password-input">
      <input
        {...props}
        aria-label={ariaLabel}
        type={visible ? "text" : "password"}
        value={props.value}
        onChange={(event) => onChange(event.target.value)}
      />
      <button
        type="button"
        className="password-visibility"
        aria-label={visible ? "Hide password" : "Show password"}
        onClick={() => setVisible((current) => !current)}
      >
        <EyeIcon hidden={visible} />
      </button>
    </span>
  );
}
