export function EyeIcon({ hidden }: { hidden: boolean }) {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {hidden ? (
        <>
          <path d="m3 3 18 18" />
          <path d="M10.6 10.6a2 2 0 0 0 2.8 2.8" />
          <path d="M9.9 4.3A10.8 10.8 0 0 1 12 4c5.5 0 9.5 5 9.5 5s-1.2 1.5-3.2 2.9M6.2 6.2C3.8 7.7 2.5 9 2.5 9s4 5 9.5 5c.7 0 1.3-.1 1.9-.2" />
        </>
      ) : (
        <>
          <path d="M2.5 9s4-5 9.5-5 9.5 5 9.5 5-4 5-9.5 5-9.5-5-9.5-5Z" />
          <circle cx="12" cy="9" r="2" />
        </>
      )}
    </svg>
  );
}
