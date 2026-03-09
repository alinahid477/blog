sudo podman login registry.redhat.io -u "xxxxxxxxxxxxx" -p "xxxxxxxxxxxxxxxxxxxx"


sudo rm -rf qcow2/

sudo podman rmi quay.io/alitestseverything/kiosk:v1

sudo podman build --network host -f Dockerfile -t quay.io/alitestseverything/kiosk:v1 .

sudo podman run --rm -it --privileged -v /var/lib/containers/storage:/var/lib/containers/storage -v ./:/output --security-opt label=type:unconfined_t --pull newer registry.redhat.io/rhel9/bootc-image-builder:9.4 --local --type qcow2 quay.io/alitestseverything/kiosk:v1

sudo chmod 777 -R qcow2/

sudo qemu-system-x86_64 \
  -M accel=kvm \
  -cpu host \
  -smp 2 \
  -m 4096 \
  -bios /usr/share/OVMF/OVMF_CODE.fd \
  -drive file="./qcow2/disk.qcow2",if=virtio,format=qcow2,cache=writeback \
  -nic user,model=virtio-net-pci \
  -serial stdio