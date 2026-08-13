export const getUiAssetPath = (path: string): string => {
  const base = process.env.NODE_ENV === "development" ? "" : "/ui";
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
};
