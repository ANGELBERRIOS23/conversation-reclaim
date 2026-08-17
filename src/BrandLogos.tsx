import type { ReactElement } from "react";
import { SiClaude, SiCursor, SiGithubcopilot, SiGooglegemini } from "@icons-pack/react-simple-icons";
import { Boxes, Image as ImageIcon } from "lucide-react";

type LogoProps = { size?: number; className?: string };

export function ClaudeLogo({ size = 22, className }: LogoProps) {
  return <SiClaude title="Claude" size={size} className={className} />;
}

export function GeminiLogo({ size = 22, className }: LogoProps) {
  return <SiGooglegemini title="Google Gemini" size={size} className={className} />;
}

export function MediaLogo({ size = 22, className }: LogoProps) {
  return <ImageIcon aria-label="Temporary media" size={size} className={className} />;
}

export function CursorLogo({ size = 22, className }: LogoProps) {
  return <SiCursor title="Cursor" size={size} className={className} />;
}

export function CopilotLogo({ size = 22, className }: LogoProps) {
  return <SiGithubcopilot title="GitHub Copilot" size={size} className={className} />;
}

export function OpenCodeLogo({ size = 22, className }: LogoProps) {
  return <svg role="img" aria-label="OpenCode" width={size} height={size} viewBox="0 0 24 24" className={className}>
    <path fill="currentColor" fillRule="evenodd" d="M18 19.5H6v-15h12v15Zm-3-12H9v9h6v-9Z" />
    <path fill="currentColor" opacity=".42" d="M9 10.5h6v6H9z" />
  </svg>;
}

export function CodexLogo({ size = 22, className }: LogoProps) {
  return <svg role="img" aria-label="OpenAI Codex" width={size} height={size} viewBox="0 0 24 24" className={className}>
    <path fill="currentColor" d="M22.282 9.821a6 6 0 0 0-.516-4.91 6.05 6.05 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a6 6 0 0 0-3.998 2.9 6.05 6.05 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.05 6.05 0 0 0 6.515 2.9A6 6 0 0 0 13.26 24a6.06 6.06 0 0 0 5.772-4.206 6 6 0 0 0 3.997-2.9 6.06 6.06 0 0 0-.747-7.073M13.26 22.43a4.48 4.48 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.8.8 0 0 0 .392-.681v-6.737l2.02 1.168a.07.07 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494M3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.77.77 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646M2.34 7.896a4.5 4.5 0 0 1 2.366-1.973V11.6a.77.77 0 0 0 .388.677l5.815 3.354-2.02 1.168a.08.08 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.872zm16.597 3.855-5.833-3.387L15.119 7.2a.08.08 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.676 8.105v-5.678a.79.79 0 0 0-.407-.667m2.01-3.023-.141-.085-4.774-2.782a.78.78 0 0 0-.785 0L9.409 9.23V6.897a.07.07 0 0 1 .028-.061l4.83-2.787a4.5 4.5 0 0 1 6.68 4.66zM8.307 12.863l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08-4.778 2.758a.8.8 0 0 0-.393.681zm1.098-2.365 2.602-1.5 2.607 1.5v2.999l-2.598 1.5-2.607-1.5Z" />
  </svg>;
}

const logos: Record<string, (props: LogoProps) => ReactElement> = {
  claude: ClaudeLogo,
  codex: CodexLogo,
  opencode: OpenCodeLogo,
  gemini: GeminiLogo,
  media: MediaLogo,
  cursor: CursorLogo,
  copilot: CopilotLogo,
};

export function BrandLogo({ logo, ...props }: LogoProps & { logo: string }) {
  const Logo = logos[logo];
  return Logo ? <Logo {...props} /> : <Boxes aria-label={logo} {...props} />;
}
