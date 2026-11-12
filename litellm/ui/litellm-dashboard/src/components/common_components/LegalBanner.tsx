"use client";

import React from "react";
import { getUiAssetPath } from "@/utils/uiAssetPath";

export default function LegalBanner() {
  return (
    <div className="relative w-full border-b border-gray-200 bg-white/90">
      <div className="flex items-center gap-6 px-6 py-3">
        <img
          src={getUiAssetPath("/assets/aisc.png")}
          alt="KI Service Zentrum"
          className="h-12 w-auto object-contain"
        />
        <img src={getUiAssetPath("/assets/BMFTR.png")} alt="BMFTR" className="h-16 w-auto object-contain" />
      </div>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
        <div className="text-xl font-semibold leading-tight text-gray-900">AI Model Hub</div>
        <div className="text-sm leading-tight text-gray-600">by KI-Servicezentrum Berlin-Brandenburg</div>
      </div>
    </div>
  );
}
