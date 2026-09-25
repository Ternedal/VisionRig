"""Operator CLI for creating and maintaining encrypted .mrvision profiles."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .profile import (
    MrVisionError,
    create_profile,
    profile_ref,
    revoke_enrollment,
)
from .profile_builder import build_embedding_encoder, enroll_image_files
from .profile_io import (
    ProfileLoadError,
    initialize_encrypted_profile,
    load_encrypted_profile,
    save_encrypted_profile,
)


def _profile_summary(profile) -> dict[str, object]:
    return {
        "schema": "visionrig/mrvision-profile-summary/v1",
        "profile_id": profile.profile_id,
        "profile_ref": profile_ref(profile),
        "revision": profile.revision,
        "parent_profile_ref": profile.parent_profile_ref,
        "created_at": profile.created_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
        "active_enrollments": sum(
            1 for item in profile.enrollments if not item.revoked
        ),
        "revoked_enrollments": sum(
            1 for item in profile.enrollments if item.revoked
        ),
        "identity_authority": False,
        "raw_pixels_persisted": False,
        "enrollments": [
            {
                "enrollment_id": item.enrollment_id,
                "kind": item.kind,
                "label": item.label,
                "subject_ref": item.subject_ref,
                "embedding_model_id": item.embedding_model_id,
                "vector_dimensions": len(item.vector),
                "quality": item.quality,
                "source_ref": item.source_ref,
                "enrolled_at": item.enrolled_at.isoformat(),
                "revoked": item.revoked,
                "revocation_reason": item.revocation_reason,
            }
            for item in profile.enrollments
        ],
    }


def _add_profile_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True, help="encrypted .mrvision file")
    parser.add_argument(
        "--key-file",
        required=True,
        help="local file containing the 32-byte profile key",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and maintain encrypted VisionRig .mrvision profiles"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create a new encrypted profile")
    _add_profile_paths(init)

    enroll = commands.add_parser(
        "enroll",
        help="derive embeddings from curated images and enroll them",
    )
    _add_profile_paths(enroll)
    enroll.add_argument("--embedding-manifest", required=True)
    enroll.add_argument(
        "--kind",
        required=True,
        choices=("face", "body", "object", "place"),
    )
    enroll.add_argument("--label", required=True)
    enroll.add_argument("--subject-ref")
    enroll.add_argument(
        "--image",
        action="append",
        required=True,
        dest="images",
        help="image sample; repeat for multiple enrollment samples",
    )
    enroll.add_argument(
        "--quality",
        type=float,
        default=0.85,
        help="operator-assessed sample quality in 0..1 (default 0.85)",
    )
    enroll.add_argument(
        "--crop",
        nargs=4,
        type=float,
        metavar=("X", "Y", "WIDTH", "HEIGHT"),
        help="optional normalized crop applied to every sample",
    )
    enroll.add_argument("--cpu", action="store_true")

    revoke = commands.add_parser("revoke", help="revoke one enrollment")
    _add_profile_paths(revoke)
    revoke.add_argument("--enrollment-id", required=True)
    revoke.add_argument("--reason", required=True)

    inspect = commands.add_parser(
        "inspect",
        help="show profile metadata without exposing embedding vectors",
    )
    _add_profile_paths(inspect)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        profile_path = Path(args.profile)
        key_path = Path(args.key_file)

        if args.command == "init":
            profile = create_profile()
            initialize_encrypted_profile(profile, profile_path, key_path)
            print(json.dumps(_profile_summary(profile), indent=2, sort_keys=True))
            return

        profile = load_encrypted_profile(profile_path, key_path)

        if args.command == "inspect":
            print(json.dumps(_profile_summary(profile), indent=2, sort_keys=True))
            return

        if args.command == "revoke":
            updated = revoke_enrollment(
                profile,
                args.enrollment_id,
                reason=args.reason,
            )
            if updated is not profile:
                save_encrypted_profile(updated, profile_path, key_path)
            print(json.dumps(_profile_summary(updated), indent=2, sort_keys=True))
            return

        if args.command == "enroll":
            encoder = build_embedding_encoder(
                args.embedding_manifest,
                prefer_cuda=not args.cpu,
            )
            crop = tuple(args.crop) if args.crop is not None else None
            updated = enroll_image_files(
                profile,
                image_paths=args.images,
                encoder=encoder,
                kind=args.kind,
                label=args.label,
                subject_ref=args.subject_ref,
                quality=args.quality,
                crop=crop,
            )
            if updated is not profile:
                save_encrypted_profile(updated, profile_path, key_path)
            print(json.dumps(_profile_summary(updated), indent=2, sort_keys=True))
            return

        raise RuntimeError(f"unsupported command: {args.command}")
    except (MrVisionError, ProfileLoadError, OSError, ValueError) as exc:
        raise SystemExit(f"visionrig-profile: {exc}") from exc


if __name__ == "__main__":
    main()
