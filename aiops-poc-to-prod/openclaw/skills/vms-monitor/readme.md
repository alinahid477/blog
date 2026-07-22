

https://console.redhat.com/docs/api/inventory/v1#operations-hosts-api\.host\.get_host_list

 {"system_profile": ["arch", "os_type", "host_type", "number_of_cpus", "installed_packages", "enabled_services", "installed_services"]}





curl -X 'GET' \
  'https://console.redhat.com/api/inventory/v1/hosts/755040ed-e778-4268-93db-fe6c50a56614/system_profile?per_page=50&page=1' \
  -H 'accept: application/json' \
  -H 'Authorization: Bearer eyJhbGciOiJSUz'


### sync insights

```
sudo insights-client
```





### Subcription reset

```
sudo subscription-manager unregister
sudo subscription-manager clean

sudo dnf remove "katello-ca-consumer*" -y

sudo subscription-manager config --server.hostname=subscription.rhsm.redhat.com --server.prefix=/subscription --server.port=443

sudo subscription-manager repo-override --remove-all

sudo rhc connect --activationkey <YOUR_ACTIVATION_KEY> --org <YOUR_ORG_ID>

sudo dnf repolist
```


### install firewalld

```
sudo dnf install firewalld -y

sudo systemctl enable --now firewalld

sudo systemctl status firewalld


# Allow standard web traffic permanently
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https

# Reload firewalld to apply the changes immediately
sudo firewall-cmd --reload

sudo firewall-cmd --list-all


```