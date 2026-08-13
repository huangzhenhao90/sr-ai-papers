"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import FavLink from "@/components/FavLink";

const navLinkBase = "px-1.5 py-1 border-b-2 transition-colors";
const navLinkActive = "border-accent text-accent font-medium";
const navLinkInactive = "border-transparent text-stone-600 hover:text-accent";

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const active = isActive(pathname, href);
  return (
    <Link
      href={href}
      className={`${navLinkBase} ${active ? navLinkActive : navLinkInactive}`}
      aria-current={active ? "page" : undefined}
    >
      {children}
    </Link>
  );
}

export default function HeaderNav() {
  return (
    <nav className="flex gap-3 text-sm items-center">
      <NavLink href="/recent">最近发表</NavLink>
      <NavLink href="/">全部论文</NavLink>
      <FavLink />
      <NavLink href="/about">关于</NavLink>
      <a
        href="/rss.xml"
        target="_blank"
        rel="noopener noreferrer"
        title="RSS 订阅"
        className="px-1.5 py-1 border-b-2 border-transparent text-stone-600 hover:text-accent inline-flex items-center gap-1"
      >
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
          <path d="M6.18 17.82c0 1.21-.98 2.18-2.18 2.18s-2.18-.97-2.18-2.18.98-2.18 2.18-2.18 2.18.97 2.18 2.18zM4 4v3.5C12.6 7.5 16.5 11.4 16.5 20H20C20 9.5 14.5 4 4 4zm0 5.5V13c3.86 0 7 3.14 7 7h3.5C14.5 12.94 9.06 9.5 4 9.5z" />
        </svg>
        <span className="hidden sm:inline">RSS</span>
      </a>
    </nav>
  );
}
