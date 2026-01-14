"use client";

import React from "react";
import { getUiAssetPath } from "@/utils/uiAssetPath";

const BANNER_IMAGES = [
  { src: getUiAssetPath("/assets/BMFTR.png"), alt: "BMBF" },
  { src: getUiAssetPath("/assets/aisc.png"), alt: "AISC" },
];

export default function LegalBanner() {
  return (
    <div className="sticky top-0 z-50 w-full border-b border-gray-200 bg-white/90">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-center gap-6 px-4 py-2">
        {BANNER_IMAGES.map((image) => (
          <img
            key={image.src}
            src={image.src}
            alt={image.alt}
            className="h-10 w-auto object-contain"
          />
        ))}
      </div>
    </div>
  );
}
