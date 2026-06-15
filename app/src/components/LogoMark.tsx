export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="64" height="64" rx="14" fill="#4f46e5"/>
      <line x1="32" y1="32" x2="32" y2="17" stroke="white" strokeWidth="2" strokeOpacity="0.6"/>
      <line x1="32" y1="32" x2="15" y2="47" stroke="white" strokeWidth="2" strokeOpacity="0.6"/>
      <line x1="32" y1="32" x2="49" y2="47" stroke="white" strokeWidth="2" strokeOpacity="0.6"/>
      <line x1="32" y1="17" x2="15" y2="47" stroke="white" strokeWidth="1.5" strokeOpacity="0.3"/>
      <line x1="32" y1="17" x2="49" y2="47" stroke="white" strokeWidth="1.5" strokeOpacity="0.3"/>
      <line x1="15" y1="47" x2="49" y2="47" stroke="white" strokeWidth="1.5" strokeOpacity="0.3"/>
      <circle cx="32" cy="32" r="7" fill="white" fillOpacity="0.95"/>
      <circle cx="32" cy="17" r="5" fill="white" fillOpacity="0.8"/>
      <circle cx="15" cy="47" r="5" fill="white" fillOpacity="0.8"/>
      <circle cx="49" cy="47" r="5" fill="white" fillOpacity="0.8"/>
    </svg>
  )
}
