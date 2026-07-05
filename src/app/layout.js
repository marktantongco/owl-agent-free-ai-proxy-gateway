import './globals.css'

export const metadata = {
  title: 'OWL-AGENT — Free AI Proxy Gateway | Unified Synergy Installer v7.1.1',
  description: 'Free AI Proxy Gateway — One gateway for Antigravity, Claude, OpenCode, Copilot, Kiro & Hermes. Zero cost, open source, Podman-native. Optimized for Linux Ubuntu 8GB RAM.',
  keywords: 'AI proxy, free AI gateway, Antigravity, Claude, OpenCode, Copilot, Kiro, Hermes, proxy gateway, open source AI, Podman, Linux, Ubuntu, MCP server, circuit breaker',
  authors: [{ name: 'Mark Tantongco', url: 'https://github.com/marktantongco' }],
  openGraph: {
    title: 'OWL-AGENT — Free AI Proxy Gateway',
    description: 'One gateway. All AI models. Zero cost. Free-tier proxy access for 6 major AI providers.',
    url: 'https://owl-agent-free-ai-proxy-gateway.vercel.app',
    siteName: 'OWL-AGENT',
    images: [{ url: 'https://raw.githubusercontent.com/marktantongco/owl-agent-free-ai-proxy-gateway/main/docs/images/hero.png', width: 1200, height: 630 }],
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'OWL-AGENT — Free AI Proxy Gateway',
    description: 'One gateway. All AI models. Zero cost.',
    images: ['https://raw.githubusercontent.com/marktantongco/owl-agent-free-ai-proxy-gateway/main/docs/images/hero.png'],
  },
  robots: { index: true, follow: true },
  alternates: { canonical: 'https://owl-agent-free-ai-proxy-gateway.vercel.app' },
}

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "SoftwareApplication",
              "name": "OWL-AGENT",
              "version": "7.1.1",
              "description": "Free AI Proxy Gateway — Unified Synergy Installer. One gateway for Antigravity, Claude, OpenCode, Copilot, Kiro & Hermes.",
              "url": "https://github.com/marktantongco/owl-agent-free-ai-proxy-gateway",
              "applicationCategory": "DeveloperApplication",
              "operatingSystem": "Linux",
              "programmingLanguage": "Python, Bash",
              "offers": { "@type": "Offer", "price": "0", "priceCurrency": "USD" },
              "author": { "@type": "Person", "name": "Mark Tantongco", "url": "https://github.com/marktantongco" },
              "license": "https://opensource.org/licenses/MIT"
            })
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  )
}
