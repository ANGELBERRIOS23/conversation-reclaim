import type { ReactElement, SVGProps } from "react";
import { SiClaude, SiGooglegemini } from "@icons-pack/react-simple-icons";
import { Boxes } from "lucide-react";

type LogoProps = { size?: number; className?: string };

export function ClaudeLogo({ size = 22, className }: LogoProps) {
  return <SiClaude title="Claude" size={size} className={className} />;
}

export function GeminiLogo({ size = 22, className }: LogoProps) {
  return <SiGooglegemini title="Google Gemini" size={size} className={className} />;
}

export function OpenCodeLogo({ size = 22, className }: LogoProps) {
  return <svg role="img" aria-label="OpenCode" width={size} height={size} viewBox="0 0 24 24" className={className}>
    <path fill="currentColor" fillRule="evenodd" d="M18 19.5H6v-15h12v15Zm-3-12H9v9h6v-9Z" />
    <path fill="currentColor" opacity=".42" d="M9 10.5h6v6H9z" />
  </svg>;
}

export function CodexLogo({ size = 22, className }: LogoProps) {
  const props: SVGProps<SVGSVGElement> = { width: size, height: size, viewBox: "0 0 24 24", className, role: "img", "aria-label": "Codex" };
  return <svg {...props}>
    <path fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" d="M12 3.25a4.25 4.25 0 0 1 4.1 3.14 4.25 4.25 0 0 1 2.67 6.76 4.25 4.25 0 0 1-4.08 5.89A4.25 4.25 0 0 1 8 18.1a4.25 4.25 0 0 1-2.7-6.74A4.25 4.25 0 0 1 9.4 5.47 4.2 4.2 0 0 1 12 3.25Z" />
    <path fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" d="m9.4 5.47 6.7.92 2.67 6.76-4.08 5.89L8 18.1l-2.7-6.74L9.4 5.47Zm0 0 3.82 5.12m2.88-4.2-2.88 4.2m5.55 2.56-5.55-2.56m1.47 8.45-1.47-8.45M8 18.1l5.22-7.51M5.3 11.36l7.92-.77" />
  </svg>;
}

const logos: Record<string, (props: LogoProps) => ReactElement> = {
  claude: ClaudeLogo,
  codex: CodexLogo,
  opencode: OpenCodeLogo,
  gemini: GeminiLogo,
};

export function BrandLogo({ logo, ...props }: LogoProps & { logo: string }) {
  const Logo = logos[logo];
  return Logo ? <Logo {...props} /> : <Boxes aria-label={logo} {...props} />;
}
