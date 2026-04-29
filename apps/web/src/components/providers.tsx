"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { AuthBridge } from "./auth-bridge";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { refetchOnWindowFocus: false, staleTime: 10_000 },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <AuthBridge />
      {children}
    </QueryClientProvider>
  );
}
