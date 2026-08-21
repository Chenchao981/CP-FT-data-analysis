import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@ant-design/v5-patch-for-react-19";
import { App as AntApp, ConfigProvider } from "antd";

import App from "./App";
import { AuthProvider } from "./features/auth/AuthContext";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1 } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ConfigProvider theme={{ token: { colorPrimary: "#1167a8", borderRadius: 8, colorBgLayout: "#f2f5f8" } }}><AntApp><QueryClientProvider client={queryClient}><AuthProvider><App /></AuthProvider></QueryClientProvider></AntApp></ConfigProvider>
  </StrictMode>,
);
