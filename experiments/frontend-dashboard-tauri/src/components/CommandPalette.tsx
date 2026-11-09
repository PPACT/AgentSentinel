import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRefresh: () => void;
  onReconnect: () => void;
}

export function CommandPalette({ open, onOpenChange, onRefresh, onReconnect }: CommandPaletteProps) {
  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="命令面板"
      description="AgentSentinel 中控命令"
    >
      <CommandInput placeholder="输入命令或搜索…" />
      <CommandList>
        <CommandEmpty>无匹配命令</CommandEmpty>
        <CommandGroup heading="操作">
          <CommandItem
            onSelect={() => {
              onRefresh();
              onOpenChange(false);
            }}
          >
            刷新状态与任务
            <CommandShortcut>R</CommandShortcut>
          </CommandItem>
          <CommandItem
            onSelect={() => {
              onReconnect();
              onOpenChange(false);
            }}
          >
            重新连接 SSE
          </CommandItem>
          <CommandItem onSelect={() => onOpenChange(false)}>
            关闭面板
            <CommandShortcut>Esc</CommandShortcut>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
