# ADR-004: Use oshash for Stash Scene Matching

**Status**: Accepted

## Context

After downloading a video, we need to find the corresponding scene in Stash so we can apply metadata (title, performers, studio, date). Stash identifies scenes by files, and files are fingerprinted.

Stash uses multiple fingerprint types: **md5**, **oshash**, and **phash** (perceptual hash).

We need a fast, reliable way to match "the file we just downloaded" to "the scene Stash created after scanning that file."

## Decision

Use **oshash** (OpenSubtitles hash) to match downloaded files to Stash scenes.

The workflow:
1. Download video to shared `/downloads` volume.
2. Compute oshash of the downloaded file **before** telling Stash to scan.
3. Trigger `metadataScan` in Stash for the specific file path.
4. Poll `findScenes` with oshash filter until Stash has processed the file.
5. Use the returned scene ID to apply metadata.

## Alternatives Considered

### MD5 hash
- Universally understood.
- Requires reading the **entire file** to compute -- very slow for large video files (multi-GB).
- Rejected for performance reasons.

### Filename matching
- Match by filename in Stash.
- Fragile: Stash may normalize or rename files; filenames are not unique identifiers.
- Rejected as unreliable.

### phash (perceptual hash)
- Stash computes this during scan, but it requires full video decode.
- Not available immediately after scan starts (takes much longer than oshash).
- We cannot compute it ourselves without Stash's sprite generation.
- Rejected because it is not available quickly enough for our polling workflow.

## Consequences

**Positive:**
- oshash is extremely fast: reads only the first and last 64KB of the file + file size. O(1) regardless of file size.
- Stash always computes oshash during scan, so it is available as soon as the scan completes.
- The same algorithm is used by Stash, so results always match.
- Deterministic: same file always produces the same hash.

**Negative:**
- oshash is a weak hash. Two different files could theoretically collide if they have the same size and same first/last 64KB. In practice this is extremely unlikely for video files.
- If the file is modified after oshash computation (e.g., by a post-processor), the hash will not match. We compute it immediately after download and before any other processing.

## Algorithm

The OpenSubtitles hash algorithm:
1. Take the file size as a 64-bit little-endian integer.
2. Read the first 65536 bytes (64KB) of the file as 8192 little-endian uint64 values. Sum them (overflow wraps).
3. Read the last 65536 bytes of the file as 8192 little-endian uint64 values. Sum them.
4. Add the file size to the sum.
5. Return as a 16-character zero-padded lowercase hex string.

```python
import struct
import os

def compute_oshash(filepath: str) -> str:
    block_size = 65536  # 64KB
    file_size = os.path.getsize(filepath)
    hash_value = file_size

    with open(filepath, "rb") as f:
        # Read first 64KB
        buf = f.read(block_size)
        hash_value += sum(struct.unpack(f"<{len(buf)//8}Q", buf))

        # Read last 64KB
        f.seek(max(0, file_size - block_size))
        buf = f.read(block_size)
        hash_value += sum(struct.unpack(f"<{len(buf)//8}Q", buf))

    # Clamp to 64-bit
    hash_value &= 0xFFFFFFFFFFFFFFFF
    return f"{hash_value:016x}"
```

## References

- [OpenSubtitles hash algorithm](https://trac.opensubtitles.org/projects/opensubtitles/wiki/HashSourceCodes)
- [Stash source: oshash implementation](https://github.com/stashapp/stash/blob/develop/pkg/file/oshash.go)
