"use client";

import React from "react";

const FOOTER_LINKS = [
  { label: "Impressum", href: "https://aisc.hpi.de/portal/cfp/pages/imprint/" },
  { label: "Privacy", href: "https://aisc.hpi.de/portal/cfp/pages/privacy/" },
];

export default function LegalFooter() {
  return (
    <footer className="mt-auto w-full border-t border-gray-200 bg-white/90 px-4 py-3 text-xs text-gray-600">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-center gap-4">
        {FOOTER_LINKS.map((link) => (
          <a
            key={link.href}
            href={link.href}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-gray-900"
          >
            {link.label}
          </a>
        ))}
      </div>
    </footer>
  );
}
