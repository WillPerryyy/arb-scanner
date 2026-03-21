interface BadgeProps {
  children: React.ReactNode;
  variant?: "success" | "warning" | "error" | "info" | "neutral" | "purple";
  className?: string;
}

const VARIANTS = {
  success: "bg-green-900/60 text-green-300 border border-green-700",
  warning: "bg-yellow-900/60 text-yellow-300 border border-yellow-700",
  error:   "bg-red-900/60 text-red-300 border border-red-700",
  info:    "bg-blue-900/60 text-blue-300 border border-blue-700",
  neutral: "bg-gray-800 text-gray-400 border border-gray-700",
  purple:  "bg-purple-900/60 text-purple-300 border border-purple-700",
};

export function Badge({ children, variant = "neutral", className = "" }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium
        ${VARIANTS[variant]} ${className}`}
    >
      {children}
    </span>
  );
}
