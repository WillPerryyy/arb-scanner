export function Spinner({ size = 24 }: { size?: number }) {
  return (
    <div className="flex items-center justify-center py-16">
      <svg
        className="animate-spin text-blue-400"
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
      >
        <circle
          className="opacity-25"
          cx="12" cy="12" r="10"
          stroke="currentColor" strokeWidth="4"
        />
        <path
          className="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
        />
      </svg>
      <span className="ml-3 text-gray-400 text-sm">Scanning markets...</span>
    </div>
  );
}
