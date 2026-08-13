"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { readFavorites } from "@/lib/favorites";

export default function FavLink() {
  const [n, setN] = useState(0);
  useEffect(() => {
    const update = () => setN(readFavorites().size);
    update();
    const onChange = () => update();
    window.addEventListener("favorites-changed", onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener("favorites-changed", onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);
  return (
    <Link href="/favorites" className="hover:text-accent flex items-center gap-1">
      <span className="text-amber-500">★</span>
      收藏
      {n > 0 && <span className="text-xs text-stone-400">({n})</span>}
    </Link>
  );
}
