import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "@/app/App";
import "@/styles/global.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Local cache first: reopening a screen must not trigger external calls.
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

const container = document.getElementById("root");
if (!container) throw new Error("Root container missing");

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
