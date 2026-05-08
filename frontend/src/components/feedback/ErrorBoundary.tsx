import { AlertTriangle } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { logger } from "@/lib/logger";

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    logger.error("react_boundary", {
      error: error.message,
      stack: error.stack,
      componentStack: info.componentStack,
    });
  }

  handleReload = () => window.location.reload();

  render() {
    if (this.state.error) {
      return (
        <div className="grid min-h-screen place-items-center bg-background p-6">
          <div className="w-full max-w-md space-y-4">
            <Alert variant="destructive">
              <AlertTriangle className="size-4" />
              <AlertTitle>Something went wrong</AlertTitle>
              <AlertDescription>{this.state.error.message}</AlertDescription>
            </Alert>
            <Button onClick={this.handleReload} className="w-full">
              Reload page
            </Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
