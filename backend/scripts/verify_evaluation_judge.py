"""Manual lesson-24 API workflow; confirmation requires an explicitly supplied Golden file."""
import argparse
import json
from pathlib import Path
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-base', default='http://127.0.0.1:18080')
    commands = parser.add_subparsers(dest='action', required=True)
    judge = commands.add_parser('judge'); judge.add_argument('case_ids', nargs='+')
    confirm = commands.add_parser('confirm'); confirm.add_argument('case_id'); confirm.add_argument('golden_file', type=Path)
    replay = commands.add_parser('replay'); replay.add_argument('dataset_item_ids', nargs='+')
    args = parser.parse_args()
    if args.action == 'judge':
        path, payload = '/judge', {'case_ids':args.case_ids}
    elif args.action == 'confirm':
        path, payload = f'/cases/{args.case_id}/confirm', {'golden_output':json.loads(args.golden_file.read_text()), 'dataset_split':'dev', 'tags':['lesson24-review']}
    else:
        path, payload = '/replay', {'dataset_item_ids':args.dataset_item_ids}
    with httpx.Client(timeout=600) as client:
        response = client.post(f'{args.api_base}/api/evaluation{path}', json=payload)
        response.raise_for_status()
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
