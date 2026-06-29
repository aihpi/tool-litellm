const getUiAssetBase = (): string => {
  const raw = process.env.NEXT_PUBLIC_UI_ASSET_BASE;
  if (raw && raw.trim()) {
    return raw.replace(/\/+$/, "");
  }
  if (process.env.NODE_ENV === "development") {
    return "";
  }
  return "/ui";
};

export const getUiAssetPath = (path: string): string => {
  const base = getUiAssetBase();
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${base}${normalized}`;
};
