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
      <SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-zinc-800">
        <SliderPrimitive.Range className="absolute h-full bg-sky-500" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb className="block size-3.5 rounded-full border border-sky-400 bg-zinc-950 shadow transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/60" />
    </SliderPrimitive.Root>
  );
}

export { Slider };
