import { useState } from "react";
import { Menu } from "lucide-react";
import { Outlet, useLocation } from "react-router-dom";
import { CommandPaletteDialog } from "@/components/CommandPalette";
import { NovaWidget } from "@/components/nova/NovaWidget";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Sidebar } from "./Sidebar";

const SIDEBAR_WIDTH = 256;

export function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  // Nova è nascosto sulle pagine admin: chi amministra la piattaforma
  // conosce già le funzionalità a memoria.
  const hideNova = location.pathname.startsWith("/admin");

  return (
    <div className="flex min-h-screen bg-background">
      <CommandPaletteDialog />
      {!hideNova && <NovaWidget />}
      <aside
        className="hidden lg:block"
        style={{ width: SIDEBAR_WIDTH }}
      >
        <div className="fixed inset-y-0 start-0" style={{ width: SIDEBAR_WIDTH }}>
          <Sidebar />
        </div>
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-72 p-0">
          <Sidebar onNavigate={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      <div className="flex min-h-screen flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border bg-background/80 px-4 backdrop-blur lg:hidden">
          <Button variant="ghost" size="icon" onClick={() => setMobileOpen(true)} aria-label="Menu">
            <Menu className="size-5" />
          </Button>
        </header>
        <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-10">
          <div className="mx-auto w-full max-w-6xl">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
