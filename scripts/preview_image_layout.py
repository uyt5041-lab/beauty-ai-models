"""Print layout instructions without loading credentials or making API calls."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.image_generation import LayoutSelection,get_layout
from src.image_generation.layout_profiles import render_layout_instruction


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aspect-ratio',required=True,choices=['3:4','4:5','9:16'])
    parser.add_argument('--placement',required=True,choices=['feed','reels','story'])
    args=parser.parse_args()
    selection=LayoutSelection(aspect_ratio=args.aspect_ratio,placement=args.placement)
    profile=get_layout(selection.aspect_ratio,selection.placement)
    print(json.dumps({'layout_selection':selection.model_dump(),'generation_size':profile.native,
                      'final_size':profile.final,'prompt_suffix':render_layout_instruction(profile),
                      'provider_calls':0},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
