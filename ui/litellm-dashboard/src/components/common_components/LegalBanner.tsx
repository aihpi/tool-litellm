"use client";

import React from "react";

export default function LegalBanner() {
  return (
    <div className="w-full border-b border-gray-200 bg-white/90">
      <div className="mx-auto flex w-full max-w-6xl flex-col items-center justify-center px-4 py-2 text-center">
        <span className="text-base font-semibold leading-tight text-gray-900">AI Model Hub</span>
        <span className="text-xs leading-tight text-gray-600">by KI-Servicezentrum Berlin-Brandenburg</span>
      </div>
    </div>
  );
}
