"use client";

import React from "react";
import { ConfigProvider } from "antd";

const themeTokens = {
  colorPrimary: "#dd6108",
  colorPrimaryHover: "#c45507",
  colorPrimaryActive: "#a94a06",
  colorInfo: "#dd6108",
  colorLink: "#dd6108",
  colorLinkHover: "#c45507",
  colorLinkActive: "#a94a06",
};

const Providers = ({ children }: { children: React.ReactNode }) => {
  return <ConfigProvider theme={{ token: themeTokens }}>{children}</ConfigProvider>;
};

export default Providers;
