# B3: time-to-first-value for published adk-tracegauge 0.10.1, following only the README's
# "Try it right now" section, from an empty venv with a cold pip cache.
S="C:/v1work/b3"
mkdir -p "$S" && cd "$S" || exit 1
uv venv --seed --python 3.13 venv2 > venv.log 2>&1 || { echo "venv failed"; cat venv.log; exit 1; }
PY="$S/venv2/Scripts/python.exe"
"$PY" -m ensurepip -q > /dev/null 2>&1
t0=$(date +%s%N)
"$PY" -m pip install --no-cache-dir -q adk-tracegauge==0.10.1 > install.log 2>&1; ie=$?
t1=$(date +%s%N)
"$S/venv2/Scripts/adk-tracegauge.exe" --version > version.log 2>&1
"$S/venv2/Scripts/adk-tracegauge.exe" quickstart > quickstart.log 2>&1; qe=$?
t2=$(date +%s%N)
echo "install exit=$ie seconds=$(( (t1 - t0) / 1000000 ))ms"
echo "quickstart exit=$qe seconds=$(( (t2 - t1) / 1000000 ))ms"
echo "total seconds=$(( (t2 - t0) / 1000000 ))ms"
echo "distributions=$("$PY" -m pip list --format=freeze 2>/dev/null | wc -l)"
cat version.log; echo "--- quickstart tail"; tail -15 quickstart.log
