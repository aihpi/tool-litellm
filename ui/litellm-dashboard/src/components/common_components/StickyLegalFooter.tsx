const legalLinks = [
  { label: "Imprint", href: "https://aisc.hpi.de/portal/cfp/pages/imprint/" },
  { label: "Privacy", href: "https://aisc.hpi.de/portal/cfp/pages/privacy/" },
];

export default function StickyLegalFooter() {
  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 border-t border-gray-200 bg-white/95 backdrop-blur">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-center gap-3 py-2 text-xs text-gray-600">
        {legalLinks.map((link, index) => (
          <span key={link.label} className="flex items-center gap-3">
            <a href={link.href} target="_blank" rel="noopener noreferrer" className="hover:underline">
              {link.label}
            </a>
            {index < legalLinks.length - 1 && <span className="text-gray-400">•</span>}
          </span>
        ))}
      </div>
    </div>
  );
}
