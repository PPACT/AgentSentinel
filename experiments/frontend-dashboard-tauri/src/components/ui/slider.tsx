"use client";

import * as SliderPrimitive from "@radix-ui/react-slider";
import { cn } from "cn";

function Slider({
  className,
  ...props
}: React.ComponentProps<typeof SliderPrimitive.Root>) {
  return (
    <SliderPrimitive.Root
      data-slot="slider"
      className={cn(
        "relative flex w-full touch-none select-none items-center",
        className,
      )}
      {...props}
    >
      <SliderPrimitive.Track className="relative h-[3px] w-full grow overflow-hidden rounded-full bg-white/10">
        <SliderPrimitive.Range className="absolute h-full bg-purple-500/55 shadow-[0_0_8px_rgba(168,85,247,0.28)]" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb className="block size-2.5 rounded-full border border-purple-300/70 bg-purple-100 shadow-[0_0_0_3px_rgba(168,85,247,0.10),0_0_10px_rgba(168,85,247,0.32)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/40" />
    </SliderPrimitive.Root>
  );
}

export { Slider };
