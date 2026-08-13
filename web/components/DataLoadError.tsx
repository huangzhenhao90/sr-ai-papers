type Props = {
  detail?: string;
  onRetry: () => void;
};

export default function DataLoadError({ detail, onRetry }: Props) {
  return (
    <div className="py-20 text-center">
      <p className="text-sm font-medium text-stone-700">数据加载失败</p>
      <p className="mt-1 text-xs text-stone-500">{detail || "请检查网络后重试。"}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-4 rounded border border-stone-300 bg-white px-3 py-1.5 text-sm text-accent hover:border-accent"
      >
        重新加载
      </button>
    </div>
  );
}
