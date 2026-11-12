"use client";

import React from "react";

export default function LegalFooter() {
  return (
    <footer className="mt-auto w-full border-t border-gray-200 bg-white/90 px-4 py-1 text-xs text-gray-600">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-center gap-4">
        <a href="https://aisc.hpi.de/portal/cfp/pages/imprint/" target="_blank" rel="noopener noreferrer">
          Imprint
        </a>
        <a href="https://aisc.hpi.de/portal/cfp/pages/privacy/" target="_blank" rel="noopener noreferrer">
          Privacy
        </a>
      </div>
    </footer>
  );
}
