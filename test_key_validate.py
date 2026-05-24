"""Test which of the found keys can decrypt MicroMsg.db."""
import hashlib
import hmac as hmac_mod
import struct
import sys
from Crypto.Cipher import AES

PAGE_SIZE = 4096
RESERVE = 48
IV_SIZE = 16
HMAC_SIZE = 20
KDF_ITER = 64000

db_path = r'C:\Users\BOBZHU01\OneDrive - Schenker AG\Documents\WeChat Files\wxid_skrow63hdk2v12\Msg\MicroMsg.db'

with open(db_path, 'rb') as f:
    page1 = f.read(PAGE_SIZE)

salt = page1[:16]
encrypted = page1[16:PAGE_SIZE - RESERVE]
reserve = page1[PAGE_SIZE - RESERVE:]
iv = reserve[:IV_SIZE]
stored_hmac = reserve[IV_SIZE:IV_SIZE + HMAC_SIZE]

print(f"Salt: {salt.hex()}")
print(f"IV: {iv.hex()}")
print(f"Stored HMAC (first 20 bytes): {stored_hmac.hex()}")
print()

# All candidate keys found from memory
keys_hex = [
    "03c16b7608336ff2fc72b300509372d15d22af91efb1ddfc17f031d83e0c7c8e",
    "088fbe0314cb8602b0e126be004e78cc11d0533296ee7931851919f5f968e656",
    "0b64073ca6119eba43fe9f1f82fc1f624887696d30abec47b9e4bc962209cdba",
    "0cd0b478694293bb650e8f3ad3bd252f25f6fe00eb44cdfa68eb5ec4625cba2a",
    "101556faafa7927359f906c1b97a40e45e64f623ac382affddccca44e548d8c5",
    "124ca18f3ed47627d2d9a81913f14579f8f5eb3b32de63401f8d1460dfd473af",
    "18c6606d3d946f6b50403e189a95adb7aada230cc80d36466e843cd9412025bc",
    "196adda8d47ca72f71dc2942358d0cebfb344d2ce1dd13cf5f3bd7c691fa5817",
    "1b466a61dd434956b8addaae6e1402361c3f3f1ef7e1e48230894e97549a9411",
    "1b9869da9236fdee7d8e6978be9807e57eb621cccac38f0d8c3dc8c65bc020ce",
    "1e0f5826193b0ca30909e8e0ec3506ab10a7307aef9399dd039d124cd9a42a5f",
    "1f74b3ebdc58873b0ac68557687f42fbb37b0df74a0407a38d3b7c9e65f3e50f",
    "20223d17ff21cf55483b6e9c5299961c02bed59ad33986ac390bcd451f7bdf35",
    "290f7987234c38166c3282c08ea54c97b631ec732824d0e3b2f1fbdde247614f",
    "29faf1e8b61fa4e73a2e993f58d10de2aa615e27399b4730760cf02a29f0b297",
    "2a26bf9c350c65a43efb0f78376a33a868d09ca00a58d1174ceb73bb8047bffa",
    "2ffb602470201d11ffae0e724495faee39241acac3a4dd3c7dba9965ff6d6c4f",
    "337bad699b5ec89516ce34fe1a714ee447ace9b29ea9a06750834cd8eca863ba",
    "34e7a1eb241a657daeca51f6ff9154de0a8012e224e95c7cf99e0e7e1895c7c7",
    "39d3e61bcd408a35d8783e17804bb40206fc898eeb038799252ccc2bce5229c5",
    "442b29219adf0e2dceeae643d61f4ef1cf1e236e608cfc430e359d0fa4c9da56",
    "50a2c631a882b7fccae81326a1ef5e38ddcdad5c672cdbf093cf485dfbf5c2b5",
    "5745667e51d979231cf4a9b23842ff2a59da249fb29875da3d4f31db5ca8f40e",
    "63d9c0023047e2349f9d332225a011ba1eebdf5955fde437cfe652163bc0da86",
    "7381e83085781cfc328454f7eeeaebc40098d61520c20e6176d234b93a2cb50d",
    "739d395b1ad60ebc47cde02e8b495665ef6bfd951898aa2d2c7af4bebabbafba",
    "79384d1052639044449767c64748004b3ee08575e852187d42ae12d69e41fedd",
    "79dee1d61b40d4d2226b0ae95925cdd510e8287b9d999fba3c26381a2627251e",
    "7f5467b21db0243ebd09ee337181200bb8643bb9be7278b8ae1d3df636fe36e8",
    "8371545ecad7fcd9ae11ee67caa5d04f07aa0ce7030a225104e9752d2e76cac4",
    "8523bea39954d19f849a0520c1f8bf82c96d3a2d770f619e39e010698130cba8",
    "869ea5d92aba63d34c2f550a43406e126936eace66b7a59d6f8aa7f05c0738f4",
    "940865a8cc02f8fa6913294f7fa0444dc560a075ef6940744d4a30e12a133b1e",
    "ab55cde9ce77311eaeced4e684249fb2ca6ee7dc28a1cc1ba56229c4a1a30527",
    "ad09925053922f7b2ef47c44584384a5f6534d25e62cd6fd157d221293928652",
    "c33286e76a0dce862557b13e3d55a2212ddebb12db660896973d8c352f86e4ea",
    "c6286fdd031488c9e57de4672368aec89ebf9146c4966f1360cff6f8d9852264",
    "c8787a02c5a0e86a6b7f041938dd10f29d6baee3c1bfecf9310a9433adbbeda7",
    "d106e0c649c68c9beead377f613164b84d1a607cc8c0eb8a68e23645c371b20c",
    "d1db29a09547058f48b57ec78c3bb1fec9605dae8fe84b44952a53880d92fc0d",
    "dfca57f14427942f9b772ebf170deef54f4f8629d6efdbe0929b993aae45fe0f",
    "e654c9f11d887612decfeab322bd2b9bed434f2edf9ef8138a28db4fe6608626",
    "eafbefe06a16a2cf17697700ab03069d1c7eba672ec60cb9d7e422bad2682e37",
    "f810665030f71e6da8cf3c41d4a23db357653e8f619eec31f1146639f1c5c480",
]

print(f"Testing {len(keys_hex)} keys...")
print()

# Method 1: Use key as raw passphrase -> PBKDF2 to derive enc key
# WeChat 3.x uses PBKDF2-SHA1, 64000 iterations
print("Method 1: PBKDF2-SHA1 with 64000 iterations (will be slow)...")
for i, hex_key in enumerate(keys_hex[:3]):  # Test first 3 only (slow)
    key_bytes = bytes.fromhex(hex_key)
    derived = hashlib.pbkdf2_hmac('sha1', key_bytes, salt, KDF_ITER, dklen=32)
    cipher = AES.new(derived, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted[:32])
    if decrypted[:2] == b'\x10\x00':
        print(f"  MATCH! Key {i}: {hex_key}")
        break
    if i == 0:
        print(f"  Key 0 decrypted start: {decrypted[:16].hex()}")
else:
    print("  No match in first 3 keys")

# Method 2: Use the hex string ITSELF as the passphrase (as SQLCipher PRAGMA key would)
print("\nMethod 2: Use hex key directly as AES key (no PBKDF2)...")
for i, hex_key in enumerate(keys_hex):
    key_bytes = bytes.fromhex(hex_key)
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted[:32])
    if decrypted[:2] == b'\x10\x00':
        print(f"  MATCH! Key {i}: {hex_key}")
        print(f"  Decrypted: {decrypted.hex()}")
        break
else:
    print("  No match")

# Method 3: Derive HMAC key from each candidate and validate HMAC
# In SQLCipher, HMAC key = PBKDF2(key, salt, iter*2) for SHA1
# But maybe WeChat uses the key directly for HMAC too
print("\nMethod 3: Direct HMAC-SHA1 validation (key = HMAC key)...")
for i, hex_key in enumerate(keys_hex):
    key_bytes = bytes.fromhex(hex_key)
    h = hmac_mod.new(key_bytes, digestmod=hashlib.sha1)
    h.update(encrypted)
    h.update(iv)
    h.update(struct.pack('<I', 1))  # page number
    if hmac_mod.compare_digest(h.digest(), stored_hmac):
        print(f"  HMAC MATCH! Key {i}: {hex_key}")
        break
else:
    print("  No HMAC match")

# Method 4: Try as passphrase with fewer iterations
print("\nMethod 4: PBKDF2-SHA1 with 4000 iterations...")
for i, hex_key in enumerate(keys_hex):
    key_bytes = bytes.fromhex(hex_key)
    derived = hashlib.pbkdf2_hmac('sha1', key_bytes, salt, 4000, dklen=32)
    cipher = AES.new(derived, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted[:32])
    if decrypted[:2] == b'\x10\x00':
        print(f"  MATCH! Key {i}: {hex_key}")
        break
else:
    print("  No match")

# Method 5: Key is used directly with HMAC-SHA1 to validate, then AES with PBKDF2 iter=1
print("\nMethod 5: PBKDF2-SHA1 with iter=1...")
for i, hex_key in enumerate(keys_hex):
    key_bytes = bytes.fromhex(hex_key)
    derived = hashlib.pbkdf2_hmac('sha1', key_bytes, salt, 1, dklen=32)
    cipher = AES.new(derived, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted[:32])
    if decrypted[:2] == b'\x10\x00':
        print(f"  MATCH! Key {i}: {hex_key}")
        break
else:
    print("  No match")

print("\nDone.")
