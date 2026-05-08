import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { CommandPaletteProvider } from "./components/CommandPalette";
import { ErrorBoundary } from "./components/feedback/ErrorBoundary";
import { Toaster } from "./components/ui/sonner";
import { ThemeProvider } from "./providers/ThemeProvider";
import { router } from "./routes/router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
    mutations: { retry: 0 },
  },
});

export default function App() {
  return (
    <ThemeProvider>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <CommandPaletteProvider>
              <Toaster />
              <RouterProvider router={router} />
            </CommandPaletteProvider>
          </AuthProvider>
        </QueryClientProvider>
      </ErrorBoundary>
    </ThemeProvider>
  );
}
