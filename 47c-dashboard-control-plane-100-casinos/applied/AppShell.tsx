// Companion code for "The Backend of Luck" - Chapter 47c, Operating 100 Casinos From One Dashboard.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: BUSL-1.1
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

"use client";

import * as React from "react";
import { z } from "zod";
import { cn } from "@/lib/utils";

export const AppShellPropsSchema = z.object({
  topNav: z.custom<React.ReactNode>().optional(),
  header: z.custom<React.ReactNode>().optional(),
  footer: z.custom<React.ReactNode>().optional(),
  children: z.custom<React.ReactNode>().optional(),
  className: z.string().optional(),
});

export type AppShellProps = {
  topNav?: React.ReactNode;
  sidebar?: React.ReactNode;
  header?: React.ReactNode;
  footer?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
};

/**
 * AppShell — MD3 dark layout matching legacy dashboard.html.
 *
 * Layout has two modes:
 *   - sidebar mode (preferred): fixed left rail, sticky translucent header
 *   - topNav mode (back-compat): horizontal tab strip under the header
 *
 * When both `sidebar` and `topNav` are supplied, `sidebar` wins.
 */
export function AppShell({
  topNav,
  sidebar,
  header,
  footer,
  children,
  className,
}: AppShellProps) {
  const hasSidebar = Boolean(sidebar);
  const nav: React.ReactNode = sidebar ?? topNav;
  const navIsSidebar = hasSidebar;

  return (
    <div
      className={cn("app-shell", className)}
      style={{ minHeight: "100vh", background: "var(--color-bg-canvas)" }}
    >
      {header && (
        <header
          className="chrome-header-bar"
          role="banner"
          style={{
            position: "sticky",
            top: 0,
            zIndex: 1020,
            height: "var(--chrome-header-h)",
            display: "flex",
            alignItems: "center",
            padding: "0 var(--space-xl)",
          }}
        >
          {header}
        </header>
      )}

      <div style={{ display: "flex" }}>
        {nav && navIsSidebar && (
          <aside className="chrome-sidebar" aria-label="Primary navigation">
            {nav}
          </aside>
        )}
        {nav && !navIsSidebar && (
          <nav className="app-nav" aria-label="Primary navigation">
            {nav}
          </nav>
        )}

        <main
          className={cn("app-main", hasSidebar && "app-main-with-sidebar")}
          role="main"
          style={{
            flex: 1,
            padding: "var(--space-2xl) var(--space-xl) 60px",
            paddingLeft: hasSidebar
              ? "calc(var(--chrome-sidebar-w) + var(--space-xl))"
              : "var(--space-xl)",
          }}
        >
          <div className="app-main-inner">{children}</div>
        </main>
      </div>

      {footer && <footer className="app-footer">{footer}</footer>}
    </div>
  );
}
