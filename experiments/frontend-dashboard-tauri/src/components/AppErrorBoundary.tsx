import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** 防止组件异常导致整页黑屏；显示错误与重新加载入口。 */
export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("AgentSentinel UI error", error, info);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="flex h-screen flex-col items-center justify-center gap-4 bg-zinc-950 p-8 text-zinc-200">
          <h1 className="text-lg font-semibold text-red-400">界面渲染出错</h1>
          <pre className="max-w-2xl overflow-auto rounded border border-zinc-800 bg-zinc-900 p-4 text-xs text-zinc-400">
            {this.state.error.message}
          </pre>
          <button
            type="button"
            className="rounded border border-zinc-700 px-4 py-2 text-sm hover:bg-zinc-900"
            onClick={() => window.location.reload()}
          >
            重新加载
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
