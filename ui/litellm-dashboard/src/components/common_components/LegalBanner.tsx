"use client";

import React from "react";
import { getUiAssetPath } from "@/utils/uiAssetPath";

export default function LegalBanner() {
  return (
    <div className="w-full border-b border-gray-200 bg-white/90">
      <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center justify-between gap-4 px-6 py-3">
        <div className="min-w-0">
          <div className="text-xl font-semibold leading-tight text-gray-900">AI Model Hub</div>
          <div className="text-sm leading-tight text-gray-600">by KI-Servicezentrum Berlin-Brandenburg</div>
        </div>
        <div className="flex flex-none items-center gap-6">
          <img
            src={getUiAssetPath("/assets/aisc.png")}
            alt="KI Service Zentrum"
            className="h-12 w-auto object-contain"
          />
          <img src={getUiAssetPath("/assets/BMFTR.png")} alt="BMFTR" className="h-16 w-auto object-contain" />
        </div>
      </div>
    </div>
  );
}
